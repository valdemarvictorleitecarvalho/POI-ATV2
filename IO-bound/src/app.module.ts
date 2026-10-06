import { Module } from '@nestjs/common';
import { AppController } from './app.controller';
import { IoService } from './io.service';

@Module({
  controllers: [AppController],
  providers: [IoService],
})
export class AppModule {}
